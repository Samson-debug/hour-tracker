"""The logging popup: one short-lived window. The tray app opens it; it exits when done."""
import socket, threading
from datetime import datetime, timedelta

import webview

from . import core, ui


class Api:
    """Called from the page as window.pywebview.api.<name>()."""

    def __init__(self, cfg):
        self._cfg, self._window, self._done = cfg, None, False

    def _snoozes_left(self, con):
        return max(0, self._cfg["max_snoozes"] - int(core.get_meta(con, "snooze_count") or 0))

    def state(self):
        con = core.get_db()
        try:
            cat, prod, sat = core.last_defaults(con, self._cfg)
            return {"hours": [h.strftime(core.FMT) for h in core.pending_hours(con, self._cfg)],
                    "categories": self._cfg["categories"], "fields": self._cfg["fields"],
                    "category": cat, "prod": prod, "sat": sat,
                    "snoozes_left": self._snoozes_left(con),
                    "snooze_minutes": self._cfg["snooze_minutes"]}
        finally:
            con.close()

    def save(self, count, entry):
        """Store the first `count` pending hours (entry=None skips them); return what is left."""
        con = core.get_db()
        try:
            pending = core.pending_hours(con, self._cfg)
            if pending:
                core.write(con, pending[:max(1, min(int(count), len(pending)))], pending[-1], entry)
                core.set_meta(con, "snooze_until", None)
                core.set_meta(con, "snooze_count", None)
        finally:
            con.close()
        state = self.state()
        if not state["hours"]:
            self._close(0.45)   # long enough to see the tick
        return state

    def snooze(self):
        self._record_snooze()
        self._close(0)

    def _record_snooze(self):
        con = core.get_db()
        try:
            until = datetime.now() + timedelta(minutes=self._cfg["snooze_minutes"])
            core.set_meta(con, "snooze_until", until.strftime(core.FMT))
            core.set_meta(con, "snooze_count", int(core.get_meta(con, "snooze_count") or 0) + 1)
        finally:
            con.close()

    def _close(self, delay):
        self._done = True
        threading.Timer(delay, self._window.destroy).start()

    def _on_closing(self):
        """The X button means 'later'. With no snoozes left, the window stays."""
        if self._done:
            return True
        con = core.get_db()
        try:
            if not self._snoozes_left(con):
                return False
        finally:
            con.close()
        self._record_snooze()
        self._done = True
        return True


def run(on_loaded=None):
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47653))   # never two popups at once
    except OSError:
        return
    cfg = core.load_cfg()
    con = core.get_db()
    try:
        if not core.pending_hours(con, cfg):
            return
    finally:
        con.close()

    api = Api(cfg)
    window = ui.open_window("popup", cfg["theme"], api, 500, 720, min_size=(340, 480), on_top=True)
    api._window = window
    window.events.closing += api._on_closing
    if on_loaded:
        window.events.loaded += lambda: threading.Thread(target=on_loaded, args=(window,), daemon=True).start()
    webview.start()
