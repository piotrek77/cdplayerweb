"""HTTP server: static web player and JSON API."""

import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .service import MP3_BITRATE, cd_input_args

log = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CHUNK = 64 * 1024


class App:
    def __init__(self, device, drive, library, monitor, ripper):
        self.device = device
        self.drive = drive
        self.library = library
        self.monitor = monitor
        self.ripper = ripper

    def state(self):
        status, toc, disc_id, meta, lookup = self.monitor.current()
        disc = None
        if toc and meta:
            ripped = self.library.ripped_tracks(disc_id)
            tracks = []
            for t in meta["tracks"]:
                _start, duration = toc.track_bounds(t["number"])
                tracks.append({**t, "duration": round(duration, 2), "ripped": t["number"] in ripped})
            disc = {
                "disc_id": disc_id,
                "artist": meta["artist"],
                "album": meta["album"],
                "year": meta.get("year", ""),
                "lookup": lookup,
                "has_cover": self.library.cover_path(disc_id) is not None,
                "tracks": tracks,
                "ripped_count": len(ripped),
                "duration": round((toc.leadout - toc.offsets[0]) / 75, 2),
            }
        return {"drive": status, "disc": disc, "rip": self.ripper.state()}


class Handler(BaseHTTPRequestHandler):
    server_version = "cdplayerweb"
    app = None  # set by serve()

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    # -- helpers ------------------------------------------------------------

    def _json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": message}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None

    def _send_file(self, path, content_type=None, cache="no-cache"):
        size = os.path.getsize(path)
        content_type = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end = 0, size - 1
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match and (match[1] or match[2]):
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), end) if match[2] else end
            else:
                start = max(0, size - int(match[2]))
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = f.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # -- routing ------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/":
            return self._send_file(os.path.join(STATIC_DIR, "index.html"))
        if path.startswith("/static/"):
            full = os.path.realpath(os.path.join(STATIC_DIR, path[len("/static/"):]))
            if full.startswith(STATIC_DIR + os.sep) and os.path.isfile(full):
                return self._send_file(full)
            return self._error("Nie znaleziono.", HTTPStatus.NOT_FOUND)
        if path == "/api/state":
            return self._json(self.app.state())
        match = re.fullmatch(r"/api/cover/([A-Za-z0-9._-]+)", path)
        if match:
            cover = self.app.library.cover_path(match[1])
            if cover:
                return self._send_file(cover, "image/jpeg", cache="max-age=86400")
            return self._error("Brak okładki.", HTTPStatus.NOT_FOUND)
        match = re.fullmatch(r"/api/track/([A-Za-z0-9._-]+)/(\d+)", path)
        if match:
            offset = float(parse_qs(url.query).get("t", ["0"])[0] or 0)
            return self._track(match[1], int(match[2]), offset)
        return self._error("Nie znaleziono.", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        app = self.app
        if path == "/api/rip":
            error = app.ripper.start()
            return self._error(error, HTTPStatus.CONFLICT) if error else self._json({"ok": True})
        if path == "/api/rip/cancel":
            app.ripper.cancel()
            return self._json({"ok": True})
        if path == "/api/eject":
            if app.ripper.busy():
                return self._error("Trwa zgrywanie płyty.", HTTPStatus.CONFLICT)
            app.drive.eject()
            return self._json({"ok": True})
        if path == "/api/metadata/refresh":
            if not app.monitor.refresh_metadata():
                return self._error("Brak płyty w napędzie.", HTTPStatus.CONFLICT)
            return self._json({"ok": True})
        return self._error("Nie znaleziono.", HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        if urlparse(self.path).path != "/api/metadata":
            return self._error("Nie znaleziono.", HTTPStatus.NOT_FOUND)
        _status, toc, disc_id, meta, _lookup = self.app.monitor.current()
        if not toc:
            return self._error("Brak płyty w napędzie.", HTTPStatus.CONFLICT)
        if self.app.library.ripped_tracks(disc_id) or self.app.ripper.busy():
            return self._error("Płyta jest już zgrana – opisu nie można zmienić.", HTTPStatus.CONFLICT)
        body = self._body()
        try:
            titles = {int(t["number"]): str(t["title"]).strip() for t in body["tracks"]}
            new = {
                **meta,
                "artist": str(body["artist"]).strip() or meta["artist"],
                "album": str(body["album"]).strip() or meta["album"],
                "year": str(body.get("year", "")).strip()[:4],
                "tracks": [
                    {**t, "title": titles.get(t["number"]) or t["title"]} for t in meta["tracks"]
                ],
            }
        except (TypeError, KeyError, ValueError):
            return self._error("Niepoprawne dane.")
        self.app.monitor.set_metadata(new)
        return self._json({"ok": True})

    # -- audio --------------------------------------------------------------

    def _track(self, disc_id, number, offset):
        app = self.app
        path = app.library.track_file(disc_id, number)
        if path:
            return self._send_file(path, "audio/mpeg")
        _status, toc, current_id, _meta, _lookup = app.monitor.current()
        if current_id != disc_id or not toc or not (toc.first <= number <= toc.last):
            return self._error("Tej płyty nie ma w napędzie.", HTTPStatus.NOT_FOUND)
        if self.command == "HEAD":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            return
        self._stream_from_cd(toc, number, offset)

    def _stream_from_cd(self, toc, number, offset):
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
        cmd += cd_input_args(self.app.device, toc, number, offset)
        cmd += ["-map", "0:a", "-map_chapters", "-1", "-map_metadata", "-1"]
        cmd += ["-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-f", "mp3", "pipe:1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            shutil.copyfileobj(proc.stdout, self.wfile, CHUNK)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.kill()
            proc.wait()


def serve(app, host, port):
    Handler.app = app
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    log.info("listening on http://%s:%d/", host or "0.0.0.0", port)
    httpd.serve_forever()
