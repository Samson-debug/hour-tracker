"""The History / Settings window. Opened from the tray; asking again reuses the open window."""
import json, re, socket, threading
from datetime import datetime

import webview

from . import core, ui

PORT = 47655
VIEWS = ("history", "reminders", "settings", "welcome")
DAY = "%Y-%m-%d"
SHOWN = ("categories", "fields", "ask_every_hours", "quiet_start", "quiet_end",
         "snooze_minutes", "max_snoozes", "theme", "reminder_sound", "reminder_snooze", "webhook_url",
         "phone_notify", "phone_delay", "delete_done_once", "notify_via", "telegram_bot", "welcomed", "share_template", "maker")
LIMITS = {"ask_every_hours": (1, 12), "quiet_start": (0, 23), "quiet_end": (0, 23),
          "snooze_minutes": (1, 120), "max_snoozes": (0, 10), "reminder_snooze": (1, 120), "phone_delay": (5, 60)}
FIELDS = ("remark", "productivity", "satisfaction")


class Api:
    """Called from the page as window.pywebview.api.<name>()."""

    def __init__(self, view):
        self._view, self._window, self._welcome_seen = view, None, False

    def boot(self):
        con = core.get_db()
        try:
            first = con.execute("SELECT MIN(hour_start) FROM entries").fetchone()[0]
        finally:
            con.close()
        today = datetime.now().strftime(DAY)
        from . import __version__
        share = core.share_info()
        return {"view": self._view, "today": today, "first_day": (first or today)[:10],
                "settings": self._settings(core.load_cfg()), "version": __version__, "frozen": core.FROZEN,
                "template_copy": share.get("template_copy") or "", "defaults": core.DEFAULTS["categories"]}

    # ------------------------------------------------------------ first run

    def welcome_shown(self):
        self._welcome_seen = True

    def finish_welcome(self, choice=None):
        """choice: {categories, ask_every_hours, quiet_start, quiet_end, autostart}; None keeps the defaults."""
        if choice:
            self.set_settings(choice)
        con = core.get_db()
        try:
            core.finish_welcome(con, core.load_cfg())
            cfg = core.load_cfg()
            due = core.next_due(con, cfg)   # the app's own schedule, quiet hours included
        finally:
            con.close()
        core.poke_tray()
        when = "" if not due else (f"{due:%H:%M}" if due.date() == datetime.now().date() else
                                   f"tomorrow at {due:%H:%M}" if (due.date() - datetime.now().date()).days == 1 else f"{due:%A} at {due:%H:%M}")
        return {"settings": self._settings(cfg), "first": when}

    # ------------------------------------------------------------ history

    def day(self, date):
        """Every finished hour of one day: an entry, skipped, or empty."""
        day, now = datetime.strptime(date, DAY), datetime.now()
        con = core.get_db()
        try:
            rows = {r[0]: r for r in con.execute("""SELECT hour_start, category, activity, remark,
                productivity, satisfaction, status FROM entries WHERE hour_start LIKE ?""", (date + " %",))}
            cat, prod, sat = core.last_defaults(con, core.load_cfg())
        finally:
            con.close()
        hours = []
        for h in range(24):
            start = day.replace(hour=h)
            if start >= core.floor_hour(now):
                break
            r = rows.get(start.strftime(core.FMT))
            if not r:
                hours.append({"hour": h, "state": "empty"})
            elif r[6] == "skipped":
                hours.append({"hour": h, "state": "skipped"})
            else:
                hours.append({"hour": h, "state": "entry", "category": r[1], "activity": r[2],
                              "remark": r[3], "prod": r[4], "sat": r[5]})
        return {"date": date, "today": now.strftime(DAY), "hours": hours,
                "defaults": {"category": cat, "prod": prod, "sat": sat}}

    def save(self, date, start, end, entry):
        """Write hours start..end-1 of that day (entry=None marks them skipped)."""
        day, limit = datetime.strptime(date, DAY), core.floor_hour(datetime.now())
        hours = [day.replace(hour=h) for h in range(max(0, int(start)), min(24, int(end)))]
        hours = [h for h in hours if h < limit]
        if hours:
            con = core.get_db()
            try:
                core.write(con, hours, None, entry)
            finally:
                con.close()
            threading.Thread(target=self._sync, daemon=True).start()
        return self.day(date)

    def _sync(self):
        try:
            con = core.get_db()
            try:
                core.sync(con, core.load_cfg())
            finally:
                con.close()
        except Exception:
            core.log_error()

    # ------------------------------------------------------------ reminders

    def reminders(self):
        con = core.get_db()
        try:
            items, now = core.reminders(con), datetime.now()
        finally:
            con.close()
        for r in items:
            nxt = core.next_occurrence(r, now) if r["enabled"] else None
            r["next"] = nxt.strftime(core.FMT) if nxt else None
            del r["updated_at"]
        return items

    def _change(self, action):
        con = core.get_db()
        try:
            action(con)
        finally:
            con.close()
        core.poke_tray()
        return self.reminders()

    def save_reminder(self, r):
        return self._change(lambda con: core.save_reminder(con, r))

    def toggle_reminder(self, rid, on):
        def action(con):
            r = next((x for x in core.reminders(con) if x["id"] == rid), None)
            if r:
                core.save_reminder(con, dict(r, enabled=bool(on)))
        return self._change(action)

    def delete_reminder(self, rid):
        return self._change(lambda con: core.delete_reminder(con, rid))

    def play_chime(self):
        from . import remind
        remind.play_chime()

    # ------------------------------------------------------------ google sheet

    def sheet_status(self):
        con = core.get_db()
        try:
            return core.sync_status(con, core.load_cfg())
        finally:
            con.close()

    def sync_now(self):
        cfg = core.load_cfg()
        con = core.get_db()
        try:
            core.sync(con, cfg)
            return core.sync_status(con, cfg)
        finally:
            con.close()

    def copy_script(self, template=False):
        ui.copy_text(core.sheet_script(core.load_cfg(), template=bool(template)))

    def close(self):
        threading.Timer(0.1, self._window.destroy).start()

    def open_link(self, url):
        if str(url).startswith("https://"):
            import webbrowser
            webbrowser.open(url)

    def phone_qr(self):
        """The phone page link as an SVG QR code."""
        import qrcode, qrcode.image.svg
        con = core.get_db()
        try:
            link = core.sync_status(con, core.load_cfg())["phone_link"]
        finally:
            con.close()
        if not link:
            return ""
        img = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, border=1)
        return img.to_string(encoding="unicode")

    def telegram_connect(self, token):
        cfg = core.load_cfg()
        bot = core.telegram_connect(cfg, token)
        threading.Thread(target=self._sync, daemon=True).start()   # the sheet needs the bot to send
        return {"bot": bot, "settings": self._settings(core.load_cfg())}

    def telegram_disconnect(self):
        cfg = core.load_cfg()
        cfg.update(telegram_token="", telegram_chat="", telegram_bot="", notify_via="email")
        core.save_cfg(cfg)
        threading.Thread(target=self._sync, daemon=True).start()
        return self._settings(cfg)

    def phone_test(self):
        return core.phone_test(core.load_cfg())

    # ------------------------------------------------------------ settings

    def _settings(self, cfg):
        return {**{k: cfg[k] for k in SHOWN}, "autostart": core.autostart_on()}

    def set_settings(self, change):
        cfg = core.load_cfg()
        for key, value in change.items():
            if key == "categories":
                value = list(dict.fromkeys(str(c).strip()[:30] for c in value if str(c).strip()))
                if not value:
                    continue
            elif key == "fields":
                value = {f: bool(value.get(f, True)) for f in FIELDS}
            elif key in LIMITS:
                low, high = LIMITS[key]
                value = max(low, min(high, int(value)))
            elif key == "theme":
                if value not in ("auto", "light", "dark"):
                    continue
            elif key in ("reminder_sound", "phone_notify", "delete_done_once"):
                value = bool(value)
            elif key == "share_template":
                value = str(value).strip()
                if value and not core.copy_link(value):
                    continue
            elif key == "notify_via":
                if value not in ("email", "telegram"):
                    continue
            elif key == "webhook_url":
                value = str(value).strip()
                if value and not re.fullmatch(r"https://script\.google\.com/\S+/exec", value):
                    continue
                con = core.get_db()
                try:   # a different sheet starts from scratch: send everything to it
                    if value != cfg.get("webhook_url"):
                        for k in ("sheet_v2", "sheet_cursor", "sync_ok", "sync_error"):
                            core.set_meta(con, k, None)
                        con.execute("UPDATE entries SET synced = 0")
                        con.execute("UPDATE reminders SET synced = 0")
                        con.commit()
                finally:
                    con.close()
            elif key == "autostart":
                core.set_autostart(bool(value))
                continue
            else:
                continue
            cfg[key] = value
        core.save_cfg(cfg)
        core.poke_tray()
        threading.Thread(target=self._sync, daemon=True).start()   # the phone side follows the new settings
        if "theme" in change:
            ui.tint_titlebar(self._window, ui.is_dark(cfg["theme"]))
        return self._settings(cfg)


def serve(lock, window):
    """A second 'open History/Settings' request lands here instead of opening a second window."""
    while True:
        try:
            conn, _ = lock.accept()
            with conn:
                view = conn.recv(32).decode(errors="ignore")
        except OSError:
            return
        if view in VIEWS:
            window.restore()
            window.evaluate_js(f"show({json.dumps(view)})")
            ui.grab_focus(window)


def run(view="history", on_loaded=None):
    view = view if view in VIEWS else "history"
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", PORT))
        lock.listen(2)
    except OSError:   # already open: hand the request over and leave
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=2) as s:
                s.sendall(view.encode())
        except OSError:
            pass
        return
    api = Api(view)
    window = ui.open_window("manager", core.load_cfg()["theme"], api, 640, 800, min_size=(380, 520))
    api._window = window

    threading.Thread(target=serve, args=(lock, window), daemon=True).start()
    if on_loaded:
        window.events.loaded += lambda: threading.Thread(target=on_loaded, args=(window,), daemon=True).start()
    webview.start()
    if api._welcome_seen and not core.load_cfg().get("welcomed"):
        api.finish_welcome()   # closed the first-run screen early: counts as done, with the defaults
