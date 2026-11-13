"""The tray icon: the only part that stays running. It sleeps until a prompt is due."""
import ctypes, socket, subprocess, threading, time
from datetime import datetime, timedelta

import pystray
from PIL import Image, ImageDraw

from . import core


def draw_icon(paused):
    s, white = 256, (255, 255, 255, 255)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, s - 6, s - 6), fill=(138, 136, 130, 255) if paused else (64, 80, 210, 255))
    if paused:
        d.rounded_rectangle((82, 76, 114, 180), 12, fill=white)
        d.rounded_rectangle((142, 76, 174, 180), 12, fill=white)
    else:   # clock hands with round ends
        for x, y in ((128, 60), (178, 152)):
            d.line((128, 128, x, y), fill=white, width=26)
            d.ellipse((x - 13, y - 13, x + 13, y + 13), fill=white)
        d.ellipse((115, 115, 141, 141), fill=white)
    return img.resize((64, 64), Image.LANCZOS)


class Tray:
    def __init__(self):
        self.cfg, self.cfg_seen = core.load_cfg(), core.cfg_mtime()
        self.proc = None                  # the open popup, if any (owned by the loop thread)
        self.hold_until = datetime.min    # no automatic popup before this
        self.card = None                  # the open reminder card, if any
        self.card_hold = datetime.min     # after a crashed card, wait before trying again
        self.checked_for = None           # the prompt we already looked at the sheet for
        self.pending = 0
        self.force = False                # "Log now" was clicked
        self.stopping = False
        self.wake = threading.Event()
        self.sync_lock = threading.Lock()
        self.shown_paused = self.cfg["paused"]
        self.icon = pystray.Icon(core.RUN_NAME, draw_icon(self.shown_paused), "Hour Tracker", self.menu())

    def reload(self):
        """Settings are changed by the Settings window too, so follow the file."""
        seen = core.cfg_mtime()
        if seen != self.cfg_seen:
            self.cfg, self.cfg_seen = core.load_cfg(), seen

    # ------------------------------------------------------------ menu

    def menu(self):
        Menu, Item = pystray.Menu, pystray.MenuItem
        return Menu(
            Item(lambda _: f"Log now  ({self.pending} h)" if self.pending else "Nothing to log yet",
                 self.log_now, default=True, enabled=lambda _: self.pending > 0),
            Item(lambda _: "Resume asking" if self.cfg["paused"] else "Pause asking", self.toggle_pause),
            Menu.SEPARATOR,
            Item("History", lambda: self.open_manager("history")),
            Item("Reminders", lambda: self.open_manager("reminders")),
            Item("Settings", lambda: self.open_manager("settings")),
            Menu.SEPARATOR,
            Item("Quit", self.quit))

    def log_now(self):
        self.force = True
        self.wake.set()

    def toggle_pause(self):
        self.reload()
        self.cfg["paused"] = not self.cfg["paused"]
        if not self.cfg["paused"]:   # hours that passed while paused are left empty
            self.cfg["track_from"] = core.floor_hour(datetime.now()).strftime(core.FMT)
        core.save_cfg(self.cfg)
        self.cfg_seen = core.cfg_mtime()
        self.sync()   # so the phone stops (or starts) notifying too
        self.wake.set()

    def open_manager(self, view):
        """History and Settings live in their own short-lived process, like the popup."""
        proc = subprocess.Popen(core.launch_cmd("--manager", view), cwd=str(core.APP_DIR))
        threading.Thread(target=lambda: (proc.wait(), self.sync(), self.wake.set()), daemon=True).start()

    def quit(self):
        self.stopping = True
        self.wake.set()
        self.icon.stop()

    # ------------------------------------------------------------ loop

    def open_popup(self):
        self.proc = subprocess.Popen(core.launch_cmd("--popup"), cwd=str(core.APP_DIR))
        threading.Thread(target=lambda p=self.proc: (p.wait(), self.wake.set()), daemon=True).start()

    def open_card(self):
        self.card = subprocess.Popen(core.launch_cmd("--remind"), cwd=str(core.APP_DIR))
        threading.Thread(target=lambda p=self.card: (p.wait(), self.wake.set()), daemon=True).start()

    def tick(self):
        """Do whatever is due; return how many seconds to sleep."""
        now = datetime.now()
        self.reload()
        if self.proc and self.proc.poll() is not None:
            # A crashed popup is not retried at once, so a broken install cannot loop.
            self.hold_until = now + timedelta(minutes=10 if self.proc.returncode else 1)
            self.proc = None
            self.sync()
        if self.card and self.card.poll() is not None:
            if self.card.returncode:
                self.card_hold = now + timedelta(minutes=5)
            self.card = None
            self.sync()   # a done one-time reminder may have been deleted: tell the sheet and calendar
        con = core.get_db()
        try:
            hours = core.pending_hours(con, self.cfg, now)
            pending = len(hours)
            # What the question would be about. (Not its due time: once overdue, that is "now" and changes every tick.)
            question = (hours[0], pending, core.get_meta(con, "snooze_until")) if hours else None
            force, self.force = self.force, False
            if force and pending and not self.proc:
                core.set_meta(con, "snooze_until", None)
                self.open_popup()
            due = core.next_due(con, self.cfg, now)
            reminder_due = bool(core.due_reminders(con, now))
            next_rem = core.next_reminder_at(con, now)
        finally:
            con.close()
        # Reminders ignore pause and quiet hours. While a card is up, the hour prompt waits.
        if reminder_due and not self.card and now >= self.card_hold:
            self.open_card()
        if (due and due <= now and pending and not self.proc and not self.card and now >= self.hold_until
                and self.cfg.get("welcomed")):   # no questions while the first-run screen is still open
            if self.cfg.get("webhook_url") and self.checked_for != question:
                # The hour may have been logged on the phone: look before asking (a few seconds at most).
                self.checked_for = question
                self.sync(wait=10)
                return 1
            self.open_popup()

        def when(t):
            return f"{t:%H:%M}" if t.date() == now.date() else f"{t:%a %H:%M}"
        if self.cfg["paused"]:
            tip = "Hour Tracker · paused"
        elif self.proc:
            tip = "Hour Tracker · waiting for your answer"
        else:
            tip = f"Hour Tracker · next prompt {when(max(due, self.hold_until))}"
        if next_rem:
            tip += f" · reminder {when(next_rem)}"
        if tip != self.icon.title:
            self.icon.title = tip
        if self.cfg["paused"] != self.shown_paused:
            self.shown_paused = self.cfg["paused"]
            self.icon.icon = draw_icon(self.shown_paused)
            self.icon.update_menu()
        if pending != self.pending:
            if pending < self.pending:   # hours got logged (by any window): send them now
                self.sync()
            self.pending = pending
            self.icon.update_menu()

        waits = [30]
        if due and not self.proc and not self.card:
            waits.append((max(due, self.hold_until) - now).total_seconds())
        if next_rem:
            waits.append((next_rem - now).total_seconds())
        return max(1, min(waits))

    def loop(self):
        if not self.cfg.get("welcomed"):
            con = core.get_db()
            try:
                has_data = con.execute("SELECT 1 FROM entries LIMIT 1").fetchone()
                if has_data:   # someone already using the app before the welcome screen existed
                    core.finish_welcome(con, self.cfg)
            finally:
                con.close()
            if not has_data:
                self.open_manager("welcome")
        self.sync()
        next_sync = datetime.now() + timedelta(minutes=10)
        while not self.stopping:
            try:
                wait = self.tick()
            except Exception:
                core.log_error()
                wait = 30
            if datetime.now() >= next_sync:   # every 10 minutes: send what failed, take in other devices' changes
                self.sync()
                next_sync = datetime.now() + timedelta(minutes=10)
            self.wake.wait(wait)
            self.wake.clear()

    def sync(self, wait=0):
        """Sync in the background; with wait, give it up to that many seconds before going on."""
        done = threading.Event()

        def work():
            try:
                run()
            finally:
                done.set()

        def run():
            if not self.sync_lock.acquire(blocking=False):
                return
            try:
                con = core.get_db()
                try:
                    core.sync(con, self.cfg)
                finally:
                    con.close()
            except Exception:
                core.log_error()
            finally:
                self.sync_lock.release()
        threading.Thread(target=work, daemon=True).start()
        if wait:
            done.wait(wait)

    def start(self):
        def setup(icon):
            icon.visible = True
            threading.Thread(target=self.loop, daemon=True).start()
        self.icon.run(setup=setup)


def wait_for_taskbar(limit=90):
    """Right after logging in, the taskbar may not exist yet; an icon added then can be lost."""
    find = ctypes.windll.user32.FindWindowW
    for waited in range(limit):
        if find("Shell_TrayWnd", None):
            return waited
        time.sleep(1)
    return limit


def run():
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47654))   # one tray icon only
        lock.listen(4)
    except OSError:
        core.startup_log("another Hour Tracker is already running, so this one closes")
        return
    waited = wait_for_taskbar()
    core.startup_log("taskbar ready" + (f" after waiting {waited} s" if waited else ""))
    threading.Thread(target=core.ensure_shortcut, daemon=True).start()
    current = {}

    def listen():   # other windows connect here to wake the tray after a change
        while True:
            try:
                lock.accept()[0].close()
            except OSError:
                return
            if current.get("tray"):
                current["tray"].wake.set()
    threading.Thread(target=listen, daemon=True).start()

    for attempt in range(1, 4):   # if the icon fails to start, try again a little later
        tray = current["tray"] = Tray()
        try:
            core.startup_log("tray icon running" + (f" (attempt {attempt})" if attempt > 1 else ""))
            tray.start()
        except Exception as e:
            core.log_error()
            core.startup_log(f"tray icon failed: {e!r}")
        else:
            if tray.stopping:
                core.startup_log("quit from the menu")
                return
            core.startup_log("tray icon ended without Quit")
        tray.stopping = True
        tray.wake.set()
        time.sleep(10)
        wait_for_taskbar()
    core.startup_log("gave up after 3 attempts")
