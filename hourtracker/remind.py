"""The reminder card: centred, always on top, with a chime. It stays until you answer it."""
import math, socket, struct, threading, wave, winsound
from datetime import datetime

import webview

from . import core, ui

PORT = 47657
WIDTH = 460


def height_for(n):
    """A first guess; the page then fits the window to its content."""
    return min(640, 240 + 80 * (n - 1))


def chime_file():
    """A soft two-note chime, made once and kept next to the data."""
    path = core.data_dir() / "chime.wav"
    if path.exists():
        return path
    rate, frames = 44100, bytearray()
    for i in range(int(rate * 1.7)):
        t, v = i / rate, 0.0
        for start, f in ((0.0, 880.0), (0.17, 1318.5)):   # A5, then E6
            u = t - start
            if u >= 0:
                v += min(1, u / 0.004) * math.exp(-u * 3.0) * (
                    math.sin(2 * math.pi * f * u) + 0.22 * math.sin(4 * math.pi * f * u))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v * 0.3)) * 32767))
    tmp = path.with_name("chime.tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    tmp.replace(path)
    return path


def play_chime():
    winsound.PlaySound(str(chime_file()), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


class Api:
    """Called from the page as window.pywebview.api.<name>()."""

    def __init__(self, cfg, count):
        self._cfg, self._height, self._window, self._closed = cfg, height_for(count), None, threading.Event()

    def due(self):
        con = core.get_db()
        try:
            items = core.due_reminders(con)
        finally:
            con.close()
        now = datetime.now()
        out = [{"id": r["id"], "text": r["text"], "time": r["time"], "occurrence": r["occurrence"],
                "late": int((now - datetime.strptime(r["occurrence"], core.FMT)).total_seconds() // 60)}
               for r in items]
        if not out:
            self._close(0.35)
        return {"items": out, "snooze": self._cfg["reminder_snooze"]}

    def fit(self, height):
        height = max(180, min(640, int(height)))
        if not self._closed.is_set() and abs(height - self._height) > 2:
            self._height = height
            self._window.resize(WIDTH, height)

    def done(self, rid, occurrence):
        con = core.get_db()
        try:
            core.reminder_done(con, rid, occurrence, self._cfg["delete_done_once"])
        finally:
            con.close()
        return self.due()

    def snooze(self):
        con = core.get_db()
        try:
            for r in core.due_reminders(con):
                core.reminder_snooze(con, r["id"], self._cfg["reminder_snooze"])
        finally:
            con.close()
        self._close(0.2)   # let this call's answer reach the page before the window goes

    def _close(self, delay):
        if not self._closed.is_set():
            self._closed.set()
            winsound.PlaySound(None, 0)
            threading.Timer(delay, self._window.destroy).start()

    def _ring(self):
        """The chime, again every minute until answered (five times at most)."""
        if not self._cfg["reminder_sound"]:
            return
        for _ in range(5):
            try:
                play_chime()
            except Exception:
                core.log_error()
                return
            if self._closed.wait(60):
                return


def run(on_loaded=None):
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", PORT))   # one card at a time; it lists everything that is due
    except OSError:
        return
    cfg = core.load_cfg()
    con = core.get_db()
    try:
        count = len(core.due_reminders(con))
    finally:
        con.close()
    if not count:
        return
    api = Api(cfg, count)
    window = ui.open_window("remind", cfg["theme"], api, WIDTH, height_for(count), focus=False,
                            title="Reminder", on_top=True, frameless=True, easy_drag=False, resizable=False)
    api._window = window
    window.events.closing += lambda: not api._closed.set()   # closed from the taskbar: stop the chime
    window.events.shown += lambda: threading.Thread(target=api._ring, daemon=True).start()
    if on_loaded:
        window.events.loaded += lambda: threading.Thread(target=on_loaded, args=(window,), daemon=True).start()
    webview.start()
