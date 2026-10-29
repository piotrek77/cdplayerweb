import argparse
import logging
import os
import shutil
import sys

from .cd import Drive
from .library import Library
from .server import App, serve
from .service import DiscMonitor, Ripper


def main():
    parser = argparse.ArgumentParser(prog="cdplayer", description="Webowy odtwarzacz płyt CD.")
    parser.add_argument("--host", default=os.environ.get("CDPLAYER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CDPLAYER_PORT", 8080)))
    parser.add_argument("--device", default=os.environ.get("CDPLAYER_DEVICE", "/dev/cdrom"))
    parser.add_argument("--music-dir", default=os.environ.get("CDPLAYER_MUSIC_DIR", "~/Music/CD"),
                        help="katalog na zgrane płyty (domyślnie ~/Music/CD)")
    parser.add_argument("--data-dir", default=os.environ.get("CDPLAYER_DATA_DIR", "~/.local/share/cdplayerweb"),
                        help="katalog na opisy płyt i okładki")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg nie jest zainstalowany.")

    device = os.path.realpath(args.device)
    drive = Drive(device)
    library = Library(os.path.expanduser(args.music_dir), os.path.expanduser(args.data_dir))
    monitor = DiscMonitor(drive, library)
    ripper = Ripper(device, library, monitor)
    monitor.start()
    try:
        serve(App(device, drive, library, monitor, ripper), args.host, args.port)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
