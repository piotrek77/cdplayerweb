"""Low-level access to the CD drive: drive status, TOC and MusicBrainz disc ID.

Uses Linux CDROM ioctls directly, so no external libraries are required.
"""

import base64
import fcntl
import hashlib
import os
import struct
import subprocess
from dataclasses import dataclass

# linux/cdrom.h
CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROM_DRIVE_STATUS = 0x5326
CDROM_LBA = 0x01
CDROM_LEADOUT = 0xAA
CDSL_CURRENT = 0x7FFFFFFF

CDS_NO_INFO = 0
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4

FRAMES_PER_SECOND = 75
# Gap between the last audio session and a data track on Enhanced CDs.
DATA_TRACK_GAP = 11400
# MusicBrainz offsets include the 2 second (150 frame) lead-in.
LEAD_IN = 150


@dataclass(frozen=True)
class Toc:
    first: int
    last: int
    offsets: tuple  # start LBA of every audio track
    leadout: int  # LBA where the audio part ends

    @property
    def track_count(self):
        return self.last - self.first + 1

    def track_bounds(self, number):
        """Return (start_seconds, duration_seconds) of a track (1-based)."""
        i = number - self.first
        start = self.offsets[i]
        end = self.offsets[i + 1] if i + 1 < len(self.offsets) else self.leadout
        return start / FRAMES_PER_SECOND, (end - start) / FRAMES_PER_SECOND

    def disc_id(self):
        data = "%02X%02X%08X" % (self.first, self.last, self.leadout + LEAD_IN)
        for i in range(99):
            data += "%08X" % (self.offsets[i] + LEAD_IN if i < len(self.offsets) else 0)
        digest = base64.b64encode(hashlib.sha1(data.encode("ascii")).digest()).decode("ascii")
        return digest.translate(str.maketrans("+/=", "._-"))

    def mb_toc(self):
        values = [self.first, self.last, self.leadout + LEAD_IN]
        values += [o + LEAD_IN for o in self.offsets]
        return "+".join(str(v) for v in values)


class Drive:
    def __init__(self, device):
        self.device = device

    def _open(self):
        return os.open(self.device, os.O_RDONLY | os.O_NONBLOCK)

    def status(self):
        """Return one of: 'ok', 'no_disc', 'tray_open', 'not_ready', 'error'."""
        try:
            fd = self._open()
        except OSError:
            return "error"
        try:
            code = fcntl.ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT)
        except OSError:
            return "error"
        finally:
            os.close(fd)
        return {
            CDS_NO_DISC: "no_disc",
            CDS_TRAY_OPEN: "tray_open",
            CDS_DRIVE_NOT_READY: "not_ready",
            CDS_DISC_OK: "ok",
        }.get(code, "not_ready")

    def read_toc(self):
        """Read the table of contents. Returns None when there are no audio tracks."""
        fd = self._open()
        try:
            first, last = fcntl.ioctl(fd, CDROMREADTOCHDR, b"\0\0")[:2]
            entries = []
            for track in list(range(first, last + 1)) + [CDROM_LEADOUT]:
                # struct cdrom_tocentry { u8 track; u8 adr:4, ctrl:4; u8 format; int lba; u8 datamode; }
                buf = struct.pack("BBBiB", track, 0, CDROM_LBA, 0, 0)
                _, adr_ctrl, _, lba, _ = struct.unpack("BBBiB", fcntl.ioctl(fd, CDROMREADTOCENTRY, buf))
                is_data = bool((adr_ctrl >> 4) & 0x04)
                entries.append((track, lba, is_data))
        finally:
            os.close(fd)

        tracks, (_, leadout, _) = entries[:-1], entries[-1]
        audio = [(t, lba) for t, lba, data in tracks if not data]
        if not audio:
            return None
        # Enhanced CD: a trailing data track ends the audio session early.
        trailing_data = [lba for t, lba, data in tracks if data and t > audio[-1][0]]
        if trailing_data:
            leadout = trailing_data[0] - DATA_TRACK_GAP
        return Toc(
            first=audio[0][0],
            last=audio[-1][0],
            offsets=tuple(lba for _, lba in audio),
            leadout=leadout,
        )

    def eject(self):
        subprocess.run(["eject", self.device], check=False, capture_output=True)
