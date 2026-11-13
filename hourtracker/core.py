"""Data, schedule and sync logic shared by the tray app and the logging popup."""
import json, os, re, socket, sqlite3, sys, traceback, urllib.error, urllib.parse, urllib.request, uuid, winreg
from datetime import datetime, timedelta
from pathlib import Path

FMT = "%Y-%m-%d %H:%M"
HOUR = timedelta(hours=1)
LOOKBACK = 24  # never ask about hours older than this

FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent

DEFAULTS = {
    "webhook_url": "", "token": "",
    "snooze_minutes": 10, "max_snoozes": 3,
    "quiet_start": 23, "quiet_end": 8,
    "categories": ["Work", "Learning", "Meals", "Essential", "Social",
                   "Entertainment", "Sleep", "Commute", "Other"],
    "fields": {"remark": True, "productivity": True, "satisfaction": True},   # optional questions
    "ask_every_hours": 1,   # how many unlogged hours pile up before a prompt
    "paused": False,
    "track_from": None,     # hours before this are never asked about (set on resume)
    "theme": "auto",        # auto | light | dark
    "reminder_sound": True,
    "reminder_snooze": 10,  # minutes "Later" waits on a reminder
    "phone_notify": True,   # phone notification when an hour is still unlogged
    "phone_delay": 5,       # minutes after the PC would ask
    "notify_via": "email",  # email (to the sheet owner's Gmail) or telegram (your own bot)
    "telegram_token": "", "telegram_chat": "", "telegram_bot": "",
    "delete_done_once": True,
    "welcomed": False,      # the first-run screen has been seen
    "share_template": "",   # your template sheet's link, built into packages you give to friends
    "maker": False,
    "export": {},           # the History export choices used last time         # you make packages for friends: show "Share with friends" in the packaged app too   # a one-time reminder is deleted once marked done (each can override)
}


def data_dir():
    """HOURTRACKER_DATA if set; the app folder if it already holds data; else %APPDATA%."""
    env = os.environ.get("HOURTRACKER_DATA")
    if env:
        d = Path(env)
    elif (APP_DIR / "tracker.db").exists() or (APP_DIR / "config.json").exists():
        return APP_DIR
    else:
        d = Path(os.environ.get("APPDATA", Path.home())) / "HourTracker"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_cfg():
    cfg = dict(DEFAULTS)
    path = data_dir() / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8-sig")))
    return cfg


def save_cfg(cfg):
    path = data_dir() / "config.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def cfg_mtime():
    try:
        return (data_dir() / "config.json").stat().st_mtime
    except OSError:
        return 0


# ---------------------------------------------------------------- this app as a program

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "HourTracker"
PYTHONW = Path(sys.executable).with_name("pythonw.exe")


def launch_cmd(*args):
    """Command line that starts another part of this app (popup, history, settings)."""
    if FROZEN:
        return [sys.executable, *args]
    return [str(PYTHONW if PYTHONW.exists() else sys.executable), "-m", "hourtracker", *args]


def autostart_cmd():
    if FROZEN:
        return f'"{sys.executable}"'
    return f'"{PYTHONW}" "{APP_DIR / "Hour Tracker.pyw"}"'


def autostart_on():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0] == autostart_cmd()
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_cmd())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def startup_log(message):
    """startup.log: one line per tray start and stop, kept short. It shows why the app wasn't running."""
    try:
        import ctypes
        tick = ctypes.windll.kernel32.GetTickCount64
        tick.restype = ctypes.c_ulonglong
        path = data_dir() / "startup.log"
        lines = path.read_text(encoding="utf-8").splitlines()[-300:] if path.exists() else []
        lines.append(f"{datetime.now():%Y-%m-%d %H:%M:%S}  pid {os.getpid():>6}  "
                     f"{tick() / 60000:6.1f} min after Windows started  {message}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def ensure_shortcut():
    """The packaged app adds itself to the Start menu, so it can be found by typing its name."""
    if not FROZEN:
        return
    link = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Hour Tracker.lnk"
    marker = data_dir() / "shortcut.txt"   # where the shortcut last pointed
    try:
        if link.exists() and marker.exists() and marker.read_text(encoding="utf-8") == sys.executable:
            return
        import subprocess
        q = lambda s: "'" + str(s).replace("'", "''") + "'"
        script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({q(link)}); "
                  f"$s.TargetPath = {q(sys.executable)}; $s.WorkingDirectory = {q(Path(sys.executable).parent)}; "
                  f"$s.Description = 'Asks once an hour what you did'; $s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       creationflags=0x08000000, timeout=30, check=True)   # no console window
        marker.write_text(sys.executable, encoding="utf-8")
    except Exception:
        log_error()


def log_error():
    """pythonw has no console, so crashes go to a file next to the data."""
    try:
        with open(data_dir() / "hourtracker.log", "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now():{FMT}} {' '.join(sys.argv[1:])}\n{traceback.format_exc()}")
    except Exception:
        pass


# ---------------------------------------------------------------- database

def get_db():
    con = sqlite3.connect(data_dir() / "tracker.db", timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS entries(
        hour_start TEXT PRIMARY KEY, category TEXT, activity TEXT, remark TEXT,
        productivity INT, satisfaction INT, status TEXT, logged_at TEXT,
        synced INT DEFAULT 0)""")
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    # Reminders carry an id, update time and a deleted flag so a phone can sync them later.
    con.execute("""CREATE TABLE IF NOT EXISTS reminders(
        id TEXT PRIMARY KEY, text TEXT, time TEXT, days TEXT, date TEXT, enabled INT,
        updated_at TEXT, deleted INT DEFAULT 0, synced INT DEFAULT 0)""")
    # What this PC has already shown; never synced.
    con.execute("""CREATE TABLE IF NOT EXISTS reminder_state(
        id TEXT PRIMARY KEY, done_through TEXT, snooze_until TEXT)""")
    try:   # added later: NULL = follow the default setting, 1 / 0 = this reminder's own choice
        con.execute("ALTER TABLE reminders ADD COLUMN delete_done INT")
    except sqlite3.OperationalError:
        pass
    return con


def poke_tray():
    """Wake the tray so it picks up a change at once instead of within 30 seconds."""
    try:
        socket.create_connection(("127.0.0.1", 47654), timeout=0.5).close()
    except OSError:
        pass


def get_meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(con, key, value):
    if value is None:
        con.execute("DELETE FROM meta WHERE key = ?", (key,))
    else:
        con.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (key, str(value)))
    con.commit()


def last_defaults(con, cfg):
    row = con.execute("""SELECT category, productivity, satisfaction FROM entries
        WHERE status != 'skipped' ORDER BY hour_start DESC LIMIT 1""").fetchone()
    cat, prod, sat = row or (None, 3, 3)
    if cat not in cfg["categories"]:
        cat = cfg["categories"][0]
    return cat, prod or 3, sat or 3


def write(con, hours, newest_pending, entry):
    """Store one chunk from the popup. entry=None marks the hours as skipped.
    newest_pending=None means a History edit: an existing entry keeps its status,
    and a filled gap counts as backfilled."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")   # seconds: two devices' edits can be ordered
    for hr in hours:
        key = hr.strftime(FMT)
        if entry is None:
            vals = ("", "", "", None, None, "skipped")
        else:
            if newest_pending is None:
                old = con.execute("SELECT status FROM entries WHERE hour_start = ?", (key,)).fetchone()
                status = old[0] if old and old[0] in ("logged", "backfilled") else "backfilled"
            else:
                status = "logged" if hr == newest_pending else "backfilled"
            vals = (entry["category"], entry["activity"], entry["remark"],
                    entry["prod"], entry["sat"], status)
        con.execute("INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?,?,?,?,0)", (key, *vals, now))
    con.commit()


# ---------------------------------------------------------------- schedule

def floor_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def first_unlogged(con, cfg, now):
    """Start of the oldest hour we still want an answer for."""
    now_floor = floor_hour(now)
    last = con.execute("SELECT MAX(hour_start) FROM entries").fetchone()[0]
    start = datetime.strptime(last, FMT) + HOUR if last else now_floor - HOUR
    if cfg.get("track_from"):
        start = max(start, datetime.strptime(cfg["track_from"], FMT))
    return max(start, now_floor - LOOKBACK * HOUR)


def pending_hours(con, cfg, now=None):
    """Every completed hour that has no entry yet, oldest first."""
    now = now or datetime.now()
    start, now_floor, hours = first_unlogged(con, cfg, now), floor_hour(now), []
    while start < now_floor:
        hours.append(start)
        start += HOUR
    return hours


def out_of_quiet(dt, cfg):
    """dt itself, or the end of the quiet period if dt falls inside it."""
    qs, qe, h = cfg["quiet_start"], cfg["quiet_end"], dt.hour
    quiet = (qs <= h < qe) if qs <= qe else (h >= qs or h < qe)
    if not quiet:
        return dt
    end = dt.replace(hour=qe, minute=0, second=0, microsecond=0)
    return end if end > dt else end + timedelta(days=1)


def next_due(con, cfg, now=None):
    """When the next prompt should appear (<= now means: show it). None while paused."""
    if cfg["paused"]:
        return None
    now = now or datetime.now()
    due = first_unlogged(con, cfg, now) + max(1, int(cfg["ask_every_hours"])) * HOUR
    snooze = get_meta(con, "snooze_until")
    if snooze:
        due = max(due, datetime.strptime(snooze, FMT))
    return out_of_quiet(max(due, now), cfg)


# ---------------------------------------------------------------- export

EXPORT_COLUMNS = (("activity", "What I did"), ("remark", "Remark"),
                  ("prod", "Productivity"), ("sat", "Satisfaction"))


def export_rows(con, opts):
    """The logged hours between opts['from'] and opts['to'] (days, inclusive) in the chosen categories."""
    start = datetime.strptime(opts["from"], DAY)
    end = datetime.strptime(opts["to"], DAY) + timedelta(days=1)
    cats = set(opts.get("categories") or [])
    rows = con.execute("""SELECT hour_start, category, activity, remark, productivity, satisfaction FROM entries
        WHERE status != 'skipped' AND hour_start >= ? AND hour_start < ? ORDER BY hour_start""",
                       (start.strftime(FMT), end.strftime(FMT))).fetchall()
    return [r for r in rows if not cats or r[1] in cats]


def write_export(path, rows, columns):
    """A CSV file Excel and Google Sheets both open correctly (UTF-8 with a byte-order mark)."""
    import csv
    keep = [(i, title) for i, (key, title) in enumerate(EXPORT_COLUMNS) if key in columns]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Date", "From", "To", "Category"] + [title for _, title in keep])
        for hs, cat, act, rem, prod, sat in rows:
            dt = datetime.strptime(hs, FMT)
            values = (act, rem, prod, sat)
            w.writerow([dt.strftime(DAY), dt.strftime("%H:%M"), (dt + HOUR).strftime("%H:%M"), cat]
                       + ["" if values[i] is None else values[i] for i, _ in keep])
    return len(rows)


# ---------------------------------------------------------------- reminders
# days: weekday digits, Monday = "0" ("0123456" = every day); "" with a date = one time only.

STAMP = "%Y-%m-%d %H:%M:%S"
DAY = "%Y-%m-%d"
CATCH_UP = timedelta(hours=6)   # missed while the PC was off or asleep: still shown this long after


def reminders(con):
    keys = ("id", "text", "time", "days", "date", "enabled", "updated_at", "delete_done")
    rows = con.execute("""SELECT id, text, time, days, date, enabled, updated_at, delete_done
        FROM reminders WHERE deleted = 0 ORDER BY time, text COLLATE NOCASE""")
    return [dict(zip(keys, r), enabled=bool(r[5]), delete_done=None if r[7] is None else bool(r[7])) for r in rows]


def _at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def last_occurrence(r, now):
    """The latest time this reminder was meant to go off, up to now."""
    if not r["days"]:
        o = _at(datetime.strptime(r["date"], DAY), r["time"])
        return o if o <= now else None
    for back in range(8):
        day = now - timedelta(days=back)
        if str(day.weekday()) in r["days"] and _at(day, r["time"]) <= now:
            return _at(day, r["time"])
    return None


def next_occurrence(r, now):
    if not r["days"]:
        o = _at(datetime.strptime(r["date"], DAY), r["time"])
        return o if o > now else None
    for fwd in range(9):
        day = now + timedelta(days=fwd)
        if str(day.weekday()) in r["days"] and _at(day, r["time"]) > now:
            return _at(day, r["time"])
    return None


def _state(con, rid):
    row = con.execute("SELECT done_through, snooze_until FROM reminder_state WHERE id = ?", (rid,)).fetchone()
    return row or (None, None)


def due_reminders(con, now=None):
    """Reminders that should be on screen now, oldest first."""
    now, out = now or datetime.now(), []
    for r in reminders(con):
        occ = last_occurrence(r, now) if r["enabled"] else None
        if not occ or occ < now - CATCH_UP:
            continue
        if occ <= datetime.strptime(r["updated_at"], STAMP):
            continue   # created or changed after that time: it starts with the next one
        done, snooze = _state(con, r["id"])
        if done and occ <= datetime.strptime(done, FMT):
            continue
        if snooze and datetime.strptime(snooze, STAMP) > now:
            continue
        out.append(dict(r, occurrence=occ.strftime(FMT)))
    return sorted(out, key=lambda r: r["occurrence"])


def next_reminder_at(con, now=None):
    """The next moment a reminder may need showing, so the tray knows how long to sleep."""
    now, times = now or datetime.now(), []
    for r in reminders(con):
        if not r["enabled"]:
            continue
        nxt = next_occurrence(r, now)
        if nxt:
            times.append(nxt)
        snooze = _state(con, r["id"])[1]
        if snooze and datetime.strptime(snooze, STAMP) > now:
            times.append(datetime.strptime(snooze, STAMP))
    return min(times, default=None)


def reminder_done(con, rid, occurrence, delete_once=False):
    """Marked done for this time. A one-time reminder is then deleted if its choice (or the default) says so."""
    con.execute("INSERT OR REPLACE INTO reminder_state VALUES(?, ?, NULL)", (rid, occurrence))
    con.commit()
    r = next((x for x in reminders(con) if x["id"] == rid), None)
    if r and not r["days"] and (delete_once if r["delete_done"] is None else r["delete_done"]):
        delete_reminder(con, rid)
        return True
    return False


def reminder_snooze(con, rid, minutes, now=None):
    until = ((now or datetime.now()) + timedelta(minutes=minutes)).strftime(STAMP)
    con.execute("INSERT OR REPLACE INTO reminder_state VALUES(?, ?, ?)", (rid, _state(con, rid)[0], until))
    con.commit()


def save_reminder(con, r):
    """Add or change a reminder; returns its id. Raises ValueError on bad input."""
    text = " ".join(str(r.get("text") or "").split())[:120]
    time = str(r.get("time") or "")
    datetime.strptime(time, "%H:%M")
    days = "".join(sorted(set(str(r.get("days") or "")) & set("0123456")))
    date = None if days else datetime.strptime(str(r.get("date") or ""), DAY).strftime(DAY)
    if not text:
        raise ValueError("empty reminder")
    rid = r.get("id") or uuid.uuid4().hex
    enabled = r.get("enabled", True)
    dd = r.get("delete_done")
    con.execute("""INSERT OR REPLACE INTO reminders(id, text, time, days, date, enabled, updated_at, deleted, synced,
        delete_done) VALUES(?,?,?,?,?,?,?,0,0,?)""",
                (rid, text, time, days, date, int(bool(enabled)), datetime.now().strftime(STAMP),
                 None if dd is None else int(bool(dd))))
    con.execute("UPDATE reminder_state SET snooze_until = NULL WHERE id = ?", (rid,))
    con.commit()
    return rid


def delete_reminder(con, rid):
    con.execute("UPDATE reminders SET deleted = 1, synced = 0, updated_at = ? WHERE id = ?",
                (datetime.now().strftime(STAMP), rid))
    con.execute("DELETE FROM reminder_state WHERE id = ?", (rid,))
    con.commit()


# ---------------------------------------------------------------- sync

def sync(con, cfg):
    """Push unsynced rows to the Google Sheet. Failures stay unsynced and retry later."""
    if not str(cfg.get("webhook_url", "")).startswith("http"):
        return
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47656))   # one sync at a time, across all our windows
    except OSError:
        return
    try:
        _sync(con, cfg)
    finally:
        lock.close()


BATCH = 400   # hours per request


class SyncError(Exception):
    """A problem the person can fix; the message is shown in Settings."""


def device_id(con):
    d = get_meta(con, "device_id")
    if not d:
        d = "pc-" + uuid.uuid4().hex[:8]
        set_meta(con, "device_id", d)
    return d


def ensure_token(cfg):
    """Every install gets its own secret; the copied sheet script carries it."""
    if not cfg.get("token"):
        import secrets
        cfg["token"] = secrets.token_urlsafe(18)
        save_cfg(cfg)
    return cfg["token"]


TELEGRAM_API = os.environ.get("HOURTRACKER_TELEGRAM_API", "https://api.telegram.org")


def _telegram(token, method, body=None):
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    req = urllib.request.Request(url, json.dumps(body).encode() if body else None, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            raise SyncError("Telegram doesn’t know that bot token. Copy it again from BotFather.")
        raise SyncError(f"Telegram answered with an error ({e.code}).")
    except (urllib.error.URLError, OSError):
        raise SyncError("Could not reach Telegram. Check the internet connection.")


def telegram_connect(cfg, token):
    """Check the bot token, find the chat where you pressed Start, and say hello there."""
    token = token.strip()
    if not re.fullmatch(r"\d+:[\w-]{20,}", token):
        raise SyncError("That doesn’t look like a bot token. It looks like 123456789:AAE… and comes from BotFather.")
    me = _telegram(token, "getMe")["result"]
    chats = [u["message"]["chat"] for u in _telegram(token, "getUpdates").get("result", [])
             if u.get("message", {}).get("chat", {}).get("type") == "private"]
    if not chats:
        raise SyncError(f"Open @{me['username']} in Telegram, press Start, then press Connect again.")
    chat = chats[-1]
    _telegram(token, "sendMessage", {"chat_id": chat["id"], "text": "Connected. Hour Tracker will message you here."})
    cfg.update(telegram_token=token, telegram_chat=str(chat["id"]), telegram_bot=me["username"], notify_via="telegram")
    save_cfg(cfg)
    return me["username"]


def render_page(name, theme="auto"):
    """Read <name>.html, pulling in the shared files it marks with /*@file*/."""
    import re
    here = Path(__file__).parent
    html = (here / f"{name}.html").read_text(encoding="utf-8")
    html = re.sub(r"/\*@([\w.]+)\*/", lambda m: (here / m.group(1)).read_text(encoding="utf-8"), html)
    return html.replace('data-theme="auto"', f'data-theme="{theme}"', 1)


def sheet_script(cfg, template=False):
    """The sheet script with the phone page built in, ready to paste. With this install's secret inside,
    or - for a template sheet you share - without one: each copy then takes the secret of the first app
    that connects to it."""
    code = (Path(__file__).with_name("sheet.gs")).read_text(encoding="utf-8")
    if not template:
        code = code.replace('"%%TOKEN%%"', json.dumps(ensure_token(cfg)), 1)
    return code.replace('"%%PAGE%%"', json.dumps(render_page("phone")), 1)


def share_info():
    """share.json, put next to the app by build.py: the template sheet friends copy."""
    try:
        return json.loads((Path(__file__).with_name("share.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def copy_link(url):
    """A Google Sheets link turned into its "Make a copy" link, or None if it isn't one."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/([\w-]{20,})", url or "")
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/copy" if m else None


def finish_welcome(con, cfg):
    """After the first-run screen: a new user is first asked about the hour that ends next, not the past one."""
    if not cfg.get("welcomed"):
        if not con.execute("SELECT 1 FROM entries LIMIT 1").fetchone():
            cfg["track_from"] = floor_hour(datetime.now()).strftime(FMT)
        cfg["welcomed"] = True
        save_cfg(cfg)


def phone_settings(cfg):
    """What the sheet script needs to know to serve the phone page and time its notifications."""
    offset = datetime.now().astimezone().utcoffset()
    return {"categories": cfg["categories"], "fields": cfg["fields"],
            "ask_every_hours": cfg["ask_every_hours"], "quiet_start": cfg["quiet_start"],
            "quiet_end": cfg["quiet_end"], "paused": bool(cfg["paused"]), "track_from": cfg.get("track_from"),
            "utc_offset_min": int(offset.total_seconds() // 60) if offset else 0,
            "notify_via": cfg["notify_via"], "telegram_token": cfg["telegram_token"],
            "telegram_chat": cfg["telegram_chat"], "telegram_bot": cfg["telegram_bot"],
            "phone_notify": bool(cfg["phone_notify"]),
            "phone_delay": int(cfg["phone_delay"]), "delete_done_once": bool(cfg["delete_done_once"])}


def phone_test(cfg):
    """Ask the sheet script to send a test notification. True if it went out."""
    resp = _post(cfg, {"token": cfg["token"], "v": 2, "action": "test_notify"})
    if not resp.get("ok"):
        raise SyncError(resp.get("error") or "The sheet refused.")
    return bool(resp.get("sent"))


def _post(cfg, body):
    req = urllib.request.Request(cfg["webhook_url"], json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        if "<html" in raw[:2000].lower():
            raise SyncError("Google asked for a sign-in or permission. In Apps Script, run “setup” once, "
                            "and check the deployment’s “Who has access” is Anyone.")
        raise SyncError("That link does not lead to the Hour Tracker script.")


def _why(e):
    if isinstance(e, SyncError):
        return str(e)
    if isinstance(e, urllib.error.HTTPError):
        return f"Google answered with an error ({e.code}). Check the link."
    if isinstance(e, (urllib.error.URLError, OSError)):
        return "Could not reach Google. Check the internet connection."
    return "Something went wrong while syncing."


def _sync(con, cfg):
    try:
        for _ in range(60):          # one batch per round, until nothing is left to send
            if not _sync_once(con, cfg):
                break
        set_meta(con, "sync_ok", datetime.now().strftime(STAMP))
        set_meta(con, "sync_error", None)
    except Exception as e:
        if not isinstance(e, (SyncError, OSError)):
            log_error()
        set_meta(con, "sync_error", datetime.now().strftime(STAMP) + "|" + _why(e))


def _sync_once(con, cfg):
    """Send one batch and take in what other devices changed. True if there is more to send."""
    v2 = get_meta(con, "sheet_v2") == "1"
    hours = con.execute("""SELECT hour_start, category, activity, remark, productivity, satisfaction,
        status, logged_at FROM entries WHERE synced = 0 ORDER BY hour_start LIMIT ?""", (BATCH,)).fetchall()
    rems = con.execute("""SELECT id, text, time, days, date, enabled, updated_at, deleted, delete_done
        FROM reminders WHERE synced = 0""").fetchall() if v2 else []
    keyed = [("h" + datetime.strptime(h[0], FMT).strftime("%Y%m%d-%H"), h) for h in hours]
    body = {"token": cfg["token"], "v": 2, "device": device_id(con),
            "cursor": int(get_meta(con, "sheet_cursor") or 0),
            "hours": [{"key": k, "category": c, "activity": a, "remark": rm, "prod": p, "sat": s,
                       "status": st, "logged_at": la} for k, (hs, c, a, rm, p, s, st, la) in keyed],
            "reminders": [{"id": i, "text": t, "time": tm, "days": d, "date": dt, "enabled": bool(en),
                           "updated_at": u, "deleted": bool(dl), "delete_done": None if dd is None else bool(dd)}
                          for i, t, tm, d, dt, en, u, dl, dd in rems]}
    if v2:
        body["settings"] = phone_settings(cfg)
    else:        # what the older script reads; the new one ignores it
        body["rows"] = [[datetime.strptime(hs, FMT).strftime("%Y-%m-%d"), hs[11:16],
                         (datetime.strptime(hs, FMT) + HOUR).strftime("%H:%M"), c, a, rm, p, s, st, la, k]
                        for k, (hs, c, a, rm, p, s, st, la) in keyed]
    resp = _post(cfg, body)
    if not resp.get("ok"):
        if resp.get("error") == "wrong token":
            raise SyncError("The script in your sheet has a different secret. Copy the script again and redeploy it.")
        raise SyncError("The sheet refused the update" + (f": {resp['error']}" if resp.get("error") else "."))

    if resp.get("v") != 2:   # an older script: it stores hours, one way only
        set_meta(con, "sheet_v2", None)
        _mark_hours(con, hours)
        return len(hours) == BATCH
    if not v2:               # first contact with the new script: copy everything into its tabs once
        set_meta(con, "sheet_v2", "1")
        set_meta(con, "sheet_cursor", None)
        con.execute("UPDATE entries SET synced = 0")
        con.execute("UPDATE reminders SET synced = 0")
        con.commit()
        return True

    _mark_hours(con, hours)
    con.executemany("UPDATE reminders SET synced = 1 WHERE id = ? AND updated_at = ?",
                    [(r[0], r[6]) for r in rems])
    for h in resp.get("hours", []):
        _take_hour(con, h)
    for r in resp.get("reminders", []):
        _take_reminder(con, r)
    con.commit()
    set_meta(con, "sheet_cursor", int(resp.get("cursor") or 0))
    set_meta(con, "sheet_build", int(resp.get("build") or 2))
    set_meta(con, "phone", json.dumps(resp.get("phone") or {}))
    return len(hours) == BATCH


def _mark_hours(con, rows):
    # Only the rows we sent, and only if they were not edited again meanwhile.
    con.executemany("""UPDATE entries SET synced = 1 WHERE hour_start = ? AND category IS ?
        AND activity IS ? AND remark IS ? AND productivity IS ? AND satisfaction IS ?
        AND status IS ? AND logged_at IS ?""", rows)
    con.commit()


def _take_hour(con, h):
    """An hour changed on another device (or the sheet kept a newer copy than ours)."""
    try:
        hs = datetime.strptime(str(h["key"]), "h%Y%m%d-%H").strftime(FMT)
    except ValueError:
        return
    cur = con.execute("SELECT logged_at, synced FROM entries WHERE hour_start = ?", (hs,)).fetchone()
    if cur and not cur[1] and str(cur[0]) > str(h["logged_at"]):
        return   # our own newer edit goes up next time
    con.execute("INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?,?,?,?,1)",
                (hs, h.get("category") or "", h.get("activity") or "", h.get("remark") or "",
                 h.get("prod"), h.get("sat"), h.get("status") or "logged", str(h["logged_at"])))


def _take_reminder(con, r):
    cur = con.execute("SELECT updated_at, synced FROM reminders WHERE id = ?", (r["id"],)).fetchone()
    if cur and not cur[1] and str(cur[0]) > str(r["updated_at"]):
        return
    days = "".join(sorted(set(str(r.get("days") or "")) & set("0123456")))
    dd = r.get("delete_done")
    con.execute("""INSERT OR REPLACE INTO reminders(id, text, time, days, date, enabled, updated_at, deleted, synced,
        delete_done) VALUES(?,?,?,?,?,?,?,?,1,?)""",
                (r["id"], r.get("text") or "", r.get("time") or "00:00", days, None if days else r.get("date"),
                 int(bool(r.get("enabled"))), str(r["updated_at"]), int(bool(r.get("deleted"))),
                 None if dd is None else int(bool(dd))))
    if r.get("deleted"):
        con.execute("DELETE FROM reminder_state WHERE id = ?", (r["id"],))
    elif not cur or cur[0] != r["updated_at"]:
        con.execute("UPDATE reminder_state SET snooze_until = NULL WHERE id = ?", (r["id"],))


def sync_status(con, cfg):
    err = get_meta(con, "sync_error")
    try:
        phone = json.loads(get_meta(con, "phone") or "{}")
    except ValueError:
        phone = {}
    link = phone.get("page_url") or ""
    return {"url": cfg.get("webhook_url") or "", "two_way": get_meta(con, "sheet_v2") == "1",
            "build": int(get_meta(con, "sheet_build") or 0), "phone": phone,
            "phone_link": f"{link}?k={urllib.parse.quote(cfg.get('token') or '', safe='')}" if link else "",
            "ok_at": get_meta(con, "sync_ok"), "error_at": err.split("|", 1)[0] if err else None,
            "error": err.split("|", 1)[1] if err else None,
            "waiting": con.execute("SELECT COUNT(*) FROM entries WHERE synced = 0").fetchone()[0]}
