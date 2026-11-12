/**
 * Hour Tracker - Google Sheet script, version 2 (build 6).
 *
 * Setup, once:
 *   1. Paste this whole file into your sheet's Extensions > Apps Script and press Ctrl+S.
 *   2. In the toolbar, pick the function "setup" and press Run. Allow access when Google asks.
 *   3. Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
 *      Updating? Deploy > Manage deployments > Edit > Version: New version. The link stays the same.
 * Only someone who knows the secret below can read or write your sheet.
 *
 * Tabs: "Hours" (one row per hour) and "Reminders". Tabs made by older versions stay as they are.
 * Reminders are copied into a Google Calendar called "Hour Tracker reminders", so a phone alerts
 * even without internet. Hour notifications come by email (to you) or from your own Telegram bot.
 */
const TOKEN = "%%TOKEN%%";   // the app puts its secret here; a template sheet's copy leaves it out (see secret())
const VERSION = 2, BUILD = 6;
const PAGE = "%%PAGE%%";   // the phone page, put in by the app when it copies this script

const HOURS = ["Date", "From", "To", "Category", "Activity", "Remark", "Productivity", "Satisfaction",
               "Status", "Logged at", "Key", "Device", "Rev"];
// New columns only ever go at the end, so tabs made by older builds can be extended in place.
const REMINDERS = ["Text", "Time", "Days", "Date", "On", "Updated", "Deleted", "Id", "Device", "Rev", "Delete when done"];
const H = index(HOURS), R = index(REMINDERS);
// Kept as plain text so Sheets never turns them into numbers, dates or formulas.
const HOURS_TEXT = [H.Category, H.Activity, H.Remark, H.Status, H["Logged at"], H.Key, H.Device];
const REMINDERS_TEXT = [R.Text, R.Time, R.Days, R.Date, R.Updated, R.Id, R.Device];
const HOUR_MS = 3600000;

function index(names) {
  const o = {};
  names.forEach(function (n, i) { o[n] = i; });
  return o;
}
function props() { return PropertiesService.getScriptProperties(); }
function sheetId() { return SpreadsheetApp.getActiveSpreadsheet().getId(); }

// The secret that lets an app in. Pasted in by the app, or - for a copy of a shared template sheet -
// taken from the first app that connects to this copy. A copy never inherits the original's claim.
function secret() {
  if (TOKEN.indexOf("%%") < 0) return TOKEN;
  const p = props();
  return p.getProperty("token_sheet") === sheetId() ? p.getProperty("token") || "" : "";
}
function claim(token) {
  const p = props(), id = sheetId();
  const from = p.getProperty("token_sheet");
  if (from && from !== id) p.deleteAllProperties();   // copied together with someone else's sheet: start clean
  p.setProperty("token", token);
  p.setProperty("token_sheet", id);
}

/* ------------------------------------------------------------------ web app */

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.k === undefined) return reply({ ok: true, v: VERSION, build: BUILD, app: "hour-tracker" });
  if (!secret() || p.k !== secret() || PAGE.indexOf("<") < 0) {
    return HtmlService.createHtmlOutput("<p style='font:17px system-ui,sans-serif;padding:2em;line-height:1.5'>" +
      "This link is not complete. Scan the QR code in Hour Tracker’s Settings again.</p>")
      .addMetaTag("viewport", "width=device-width, initial-scale=1");
  }
  return HtmlService.createHtmlOutput(PAGE.replace('"%%K%%"', JSON.stringify(secret())))
    .setTitle("Hour Tracker")
    .addMetaTag("viewport", "width=device-width, initial-scale=1, viewport-fit=cover");
}

function doPost(e) {
  let req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply({ ok: false, error: "bad request" });
  }
  if (!req) return reply({ ok: false, error: "bad request" });
  if (!secret() && typeof req.token === "string" && req.token.length >= 16) {
    locked(function () { if (!secret()) claim(req.token); return {}; });
  }
  if (!secret() || req.token !== secret()) return reply({ ok: false, error: "wrong token" });
  return reply(locked(function () {
    if (req.v !== VERSION) return legacy(req.rows || []);
    if (req.action === "test_notify") return { ok: true, v: VERSION, build: BUILD, sent: notify("Test from Hour Tracker. Notifications work.", true) };
    return sync(req);
  }));
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function locked(fn) {
  const lock = LockService.getScriptLock();
  lock.waitLock(25000);
  try {
    return fn();
  } catch (err) {
    return { ok: false, error: String(err && err.message || err) };
  } finally {
    lock.releaseLock();
  }
}

/* ------------------------------------------------------------------ sync with the PC */

// Every write gets a revision number; a device asks for everything newer than the last one it saw.
function sync(req) {
  const device = String(req.device || "unknown"), cursor = Number(req.cursor || 0);
  if (req.settings) props().setProperty("settings", JSON.stringify(req.settings));
  const hours = req.hours || [], reminders = req.reminders || [];
  const w = write(hours, reminders, device);
  function since(rows, revCol, devCol) {
    return rows.filter(function (x) { return Number(x[revCol]) > cursor && String(x[devCol]) !== device; });
  }
  return {
    ok: true, v: VERSION, build: BUILD, cursor: w.cursor,
    hours: since(w.h.rows, H.Rev, H.Device).concat(w.h.kept).map(hourOut),
    reminders: since(w.r.rows, R.Rev, R.Device).concat(w.r.kept).map(reminderOut),
    phone: phoneStatus()
  };
}

function write(hours, reminders, device) {
  const p = props(), rev = Number(p.getProperty("rev") || 0) + 1;
  const h = upsert(tab("Hours", HOURS, HOURS_TEXT), HOURS.length, H.Key, H["Logged at"], hours, hourRow, rev, device, H.Key);
  const r = upsert(tab("Reminders", REMINDERS, REMINDERS_TEXT), REMINDERS.length, R.Id, R.Updated, reminders, reminderRow, rev, device, null);
  const wrote = hours.length + reminders.length > 0;
  if (wrote) p.setProperty("rev", String(rev));
  let latest = "";
  h.rows.forEach(function (x) { if (String(x[H.Key]) > latest) latest = String(x[H.Key]); });
  p.setProperty("latest_key", latest);
  r.applied.forEach(function (row) { mirrorSafe(reminderOut(row)); });
  return { h: h, r: r, cursor: wrote ? rev : rev - 1 };
}

// The app before version 2 sent {rows: [[date, from, to, category, activity, remark, prod, sat, status, logged_at, key]]}.
function legacy(rows) {
  const items = rows.map(function (r) {
    return { key: r[10], category: r[3], activity: r[4], remark: r[5], prod: r[6], sat: r[7], status: r[8], logged_at: r[9] };
  });
  write(items, [], "pc");
  return { ok: true };
}

/* ------------------------------------------------------------------ the phone page */

function auth(k) { if (!secret() || k !== secret()) throw new Error("This link is not complete."); }

function pageState(k) {
  auth(k);
  const s = settings(), now = nowLocal(), rows = readAll(tab("Hours", HOURS, HOURS_TEXT), HOURS.length);
  let last = null;
  rows.forEach(function (x) {
    if (String(x[H.Status]) !== "skipped" && (!last || String(x[H.Key]) > String(last[H.Key]))) last = x;
  });
  const cats = s.categories && s.categories.length ? s.categories : ["Work", "Other"];
  return {
    hours: pendingHours(now, s).map(function (d) { return wall(d).slice(0, 16); }),
    categories: cats, fields: s.fields || {},
    category: last && cats.indexOf(String(last[H.Category])) >= 0 ? String(last[H.Category]) : cats[0],
    prod: last ? num(last[H.Productivity]) || 3 : 3, sat: last ? num(last[H.Satisfaction]) || 3 : 3,
    latest: props().getProperty("latest_key") || "", notify: s.phone_notify !== false,
    via: s.notify_via === "telegram" ? "telegram" : "email", bot: s.telegram_bot || "", channel_ready: channelReady(s),
    delete_done_once: s.delete_done_once !== false,
    reminders: reminderList()
  };
}

function pageSave(k, count, entry) {
  auth(k);
  const res = locked(function () {
    const now = nowLocal(), pending = pendingHours(now, settings());
    if (!pending.length) return { ok: true };
    const newest = keyOf(pending[pending.length - 1]), stamp = wall(now);
    const items = pending.slice(0, Math.max(1, Math.min(Number(count) || 1, pending.length))).map(function (d) {
      const key = keyOf(d);
      return entry ? { key: key, category: entry.category, activity: entry.activity, remark: entry.remark, prod: entry.prod,
                       sat: entry.sat, status: key === newest ? "logged" : "backfilled", logged_at: stamp }
                   : { key: key, category: "", activity: "", remark: "", prod: null, sat: null, status: "skipped", logged_at: stamp };
    });
    write(items, [], "phone");
    return { ok: true };
  });
  if (!res.ok) throw new Error(res.error);
  return pageState(k);
}

function pageReminders(k) { auth(k); return reminderList(); }

function pageSaveReminder(k, r) {
  auth(k);
  return changeReminder({ id: r.id || Utilities.getUuid().replace(/-/g, ""), text: String(r.text || "").slice(0, 120),
                          time: String(r.time), days: String(r.days || ""), date: r.days ? "" : String(r.date || ""),
                          enabled: r.enabled !== false, deleted: false,
                          delete_done: r.days || r.delete_done === null || r.delete_done === undefined ? null : !!r.delete_done });
}

function pageToggleReminder(k, id, on) {
  auth(k);
  const cur = reminderList().filter(function (r) { return r.id === id; })[0];
  return cur ? changeReminder({ id: id, text: cur.text, time: cur.time, days: cur.days, date: cur.date, enabled: !!on, deleted: false, delete_done: cur.delete_done }) : reminderList();
}

function pageDeleteReminder(k, id) {
  auth(k);
  const cur = reminderList().filter(function (r) { return r.id === id; })[0];
  return cur ? changeReminder({ id: id, text: cur.text, time: cur.time, days: cur.days, date: cur.date, enabled: false, deleted: true, delete_done: cur.delete_done }) : reminderList();
}

function pageTestNotify(k) { auth(k); return notify("Test from Hour Tracker. Notifications work.", true); }

function changeReminder(r) {
  r.updated_at = wall(nowLocal());
  const res = locked(function () { write([], [r], "phone"); return { ok: true }; });
  if (!res.ok) throw new Error(res.error);
  return reminderList();
}

function reminderList() {
  const now = nowLocal();
  return readAll(tab("Reminders", REMINDERS, REMINDERS_TEXT), REMINDERS.length)
    .map(reminderOut)
    .filter(function (r) { return !r.deleted; })
    .map(function (r) {
      const n = r.enabled ? nextOccurrence(r, now) : null;
      return { id: r.id, text: r.text, time: r.time, days: r.days, date: r.date, enabled: r.enabled, delete_done: r.delete_done,
               next: n ? wall(n).slice(0, 16) : null };
    })
    .sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : a.text.localeCompare(b.text); });
}

/* ------------------------------------------------------------------ time, in the PC's local time */
// Dates here hold local wall-clock time in their UTC fields: Date.now() shifted by the PC's UTC offset.

function settings() {
  try { return JSON.parse(props().getProperty("settings") || "{}"); } catch (e) { return {}; }
}
function nowLocal() { return new Date(Date.now() + (Number(settings().utc_offset_min) || 0) * 60000); }
function floorHour(d) { return new Date(Math.floor(d.getTime() / HOUR_MS) * HOUR_MS); }
function wall(d) {
  return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()) + " " +
         pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds());
}
function keyOf(d) { return "h" + d.getUTCFullYear() + pad(d.getUTCMonth() + 1) + pad(d.getUTCDate()) + "-" + pad(d.getUTCHours()); }
function fromKey(k) {
  const m = /^h(\d{4})(\d{2})(\d{2})-(\d{2})$/.exec(k);
  return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4])) : null;
}
function fromWall(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/.exec(s || "");
  return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5])) : null;
}

// Same rule as the PC: every finished hour after the newest logged one, at most 24 back.
function firstUnlogged(now, s) {
  const floor = floorHour(now), latest = fromKey(props().getProperty("latest_key") || "");
  let start = latest ? new Date(latest.getTime() + HOUR_MS) : new Date(floor.getTime() - HOUR_MS);
  const from = fromWall(s.track_from);
  if (from && from > start) start = from;
  const cap = new Date(floor.getTime() - 24 * HOUR_MS);
  return start > cap ? start : cap;
}
function pendingHours(now, s) {
  const out = [], floor = floorHour(now);
  for (let t = firstUnlogged(now, s); t < floor; t = new Date(t.getTime() + HOUR_MS)) out.push(t);
  return out;
}
function quiet(now, s) {
  const qs = Number(s.quiet_start), qe = Number(s.quiet_end), h = now.getUTCHours();
  if (isNaN(qs) || isNaN(qe) || qs === qe) return false;
  return qs < qe ? (qs <= h && h < qe) : (h >= qs || h < qe);
}
function nextOccurrence(r, now) {
  const t = String(r.time).split(":").map(Number);
  if (!r.days) {
    const d = String(r.date).split("-").map(Number), o = new Date(Date.UTC(d[0], d[1] - 1, d[2], t[0], t[1]));
    return o > now ? o : null;
  }
  for (let i = 0; i < 9; i++) {
    const day = new Date(now.getTime() + i * 864e5), o = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), t[0], t[1]));
    if (r.days.indexOf(String((day.getUTCDay() + 6) % 7)) >= 0 && o > now) return o;
  }
  return null;
}

/* ------------------------------------------------------------------ phone notifications (email or Telegram) */

// Runs every 5 minutes (made by setup). Sends one notification per hour while hours are still unlogged,
// a few minutes after the PC would have asked - so a PC that is on gets the first chance.
function checkHour() {
  const s = settings();
  if (s.phone_notify === false || s.paused || !channelReady(s)) return;
  const now = nowLocal(), first = firstUnlogged(now, s);
  const due = first.getTime() + Math.max(1, Number(s.ask_every_hours) || 1) * HOUR_MS + (Number(s.phone_delay) || 5) * 60000;
  if (now.getTime() < due || quiet(now, s)) return;
  const pending = pendingHours(now, s);
  if (!pending.length) return;
  const mark = keyOf(floorHour(now)), p = props();
  if (p.getProperty("notified") === mark) return;
  const a = pending[0], b = new Date(pending[pending.length - 1].getTime() + HOUR_MS);
  const span = pad(a.getUTCHours()) + ":00 – " + pad(b.getUTCHours()) + ":00";
  if (notify(pending.length === 1 ? "What did you do " + span + "?" : pending.length + " hours to log · " + span, false)) p.setProperty("notified", mark);
}

function channelReady(s) {
  return s.notify_via === "telegram" ? !!(s.telegram_token && s.telegram_chat) : true;
}

function pageLink() { return ScriptApp.getService().getUrl() + "?k=" + encodeURIComponent(secret()); }

function notify(message, test) {
  const s = settings(), via = s.notify_via === "telegram" ? "telegram" : "email";
  let error = "";
  try {
    if (via === "telegram") {
      if (!channelReady(s)) throw new Error("Telegram is not connected yet. Connect it in the PC app's Settings.");
      const res = UrlFetchApp.fetch("https://api.telegram.org/bot" + s.telegram_token + "/sendMessage", {
        method: "post", contentType: "application/json", muteHttpExceptions: true,
        payload: JSON.stringify({ chat_id: s.telegram_chat, text: message,
                                  reply_markup: { inline_keyboard: [[{ text: test ? "Open Hour Tracker" : "Log it", url: pageLink() }]] } })
      });
      if (res.getResponseCode() >= 300) error = "Telegram answered " + res.getResponseCode();
    } else {
      const link = pageLink();
      MailApp.sendEmail({
        to: Session.getEffectiveUser().getEmail(), name: "Hour Tracker", subject: message,
        body: message + "\n\n" + (test ? "Open Hour Tracker: " : "Log it: ") + link,
        htmlBody: "<p style='font:16px sans-serif'>" + message + "</p><p><a href='" + link + "' style='display:inline-block;" +
                  "background:#4050d2;color:#fff;padding:10px 18px;border-radius:8px;font:600 15px sans-serif;text-decoration:none'>" +
                  (test ? "Open Hour Tracker" : "Log it") + "</a></p>"
      });
    }
  } catch (err) {
    error = String(err && err.message || err);
  }
  props().setProperty("notify_error", error);
  return !error;
}

/* ------------------------------------------------------------------ reminders in Google Calendar */

const WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"];

function calendar() {
  const p = props(), id = p.getProperty("calendar_id");
  let cal = id ? CalendarApp.getCalendarById(id) : null;
  if (!cal) {
    cal = CalendarApp.createCalendar("Hour Tracker reminders", { summary: "Made by Hour Tracker. Change reminders in the app, not here." });
    p.setProperty("calendar_id", cal.getId());
  }
  return cal;
}

function mirrorSafe(r) {
  try {
    mirror(r);
    props().setProperty("calendar_error", "");
  } catch (err) {
    props().setProperty("calendar_error", String(err && err.message || err));
  }
}

// One calendar event (series) per reminder, with a pop-up alert at its time. Rebuilt on every change.
function mirror(r) {
  const p = props(), key = "ev_" + r.id, old = p.getProperty(key);
  if (!old && (!r.enabled || r.deleted)) return;
  const cal = calendar();
  if (old) {
    try {
      const o = JSON.parse(old);
      if (o.series) cal.getEventSeriesById(o.id).deleteEventSeries(); else cal.getEventById(o.id).deleteEvent();
    } catch (e) { /* already gone */ }
    p.deleteProperty(key);
  }
  if (!r.enabled || r.deleted) return;
  const off = (Number(settings().utc_offset_min) || 0) * 60000, now = nowLocal();
  let start = nextOccurrence(r, now);
  if (!start) return;
  if (r.days) {   // the series starts on its first real day, so no stray alert on a day it should not ring
    start = nextOccurrence(r, new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) - 1)) || start;
  }
  start = new Date(start.getTime() - off);   // wall-clock -> real moment
  const end = new Date(start.getTime() + 5 * 60000);
  let ev;
  if (r.days) {
    const rec = CalendarApp.newRecurrence();
    if (r.days === "0123456") rec.addDailyRule();
    else rec.addWeeklyRule().onlyOnWeekdays(r.days.split("").map(function (d) { return CalendarApp.Weekday[WEEKDAYS[+d]]; }));
    ev = cal.createEventSeries(r.text, start, end, rec);
  } else {
    ev = cal.createEvent(r.text, start, end);
  }
  ev.removeAllReminders();
  ev.addPopupReminder(0);
  p.setProperty(key, JSON.stringify({ id: ev.getId(), series: !!r.days }));
}

/* ------------------------------------------------------------------ setup (run once from the editor) */

function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "checkHour") ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("checkHour").timeBased().everyMinutes(5).create();
  calendar();
  readAll(tab("Reminders", REMINDERS, REMINDERS_TEXT), REMINDERS.length).forEach(function (row) { mirrorSafe(reminderOut(row)); });
  props().setProperty("setup_done", BUILD + ":" + sheetId());   // a copy of this sheet must run setup itself
  Logger.log("Done. Now deploy a new version: Deploy > Manage deployments > Edit > Version: New version.");
}

function phoneStatus() {
  const p = props();
  let url = "";
  try { url = ScriptApp.getService().getUrl() || ""; } catch (e) { /* not deployed yet */ }
  return { page_url: url, setup: p.getProperty("setup_done") === BUILD + ":" + sheetId(), has_page: PAGE.indexOf("<") >= 0,
           calendar_error: p.getProperty("calendar_error") || "", notify_error: p.getProperty("notify_error") || "" };
}

/* ------------------------------------------------------------------ sheet helpers */

function tab(name, cols, textCols) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const names = [name, name + " (app)"];
  for (let k = 0; k < names.length; k++) {
    let s = ss.getSheetByName(names[k]);
    if (!s) {
      s = ss.insertSheet(names[k]);
      s.getRange(1, 1, 1, cols.length).setValues([cols]).setFontWeight("bold");
      s.setFrozenRows(1);
      textCols.forEach(function (c) { s.getRange(1, c + 1, s.getMaxRows(), 1).setNumberFormat("@"); });
      s.hideColumns(cols.indexOf("Device") + 1, 2);   // Device and Rev are for the app only
      return s;
    }
    const head = s.getRange(1, 1, 1, cols.length).getValues()[0];
    if (head.join("|") === cols.join("|")) return s;   // ours; otherwise someone else's tab of that name
    const used = head.filter(function (h) { return h !== ""; }).length;
    if (used >= 4 && head.slice(0, used).join("|") === cols.slice(0, used).join("|") && head.slice(used).every(function (h) { return h === ""; })) {
      s.getRange(1, used + 1, 1, cols.length - used).setValues([cols.slice(used)]).setFontWeight("bold");   // made by an older build
      return s;
    }
  }
  throw new Error("Rename the tab called '" + names[1] + "' so the app can make its own.");
}

function readAll(s, width) {
  const n = s.getLastRow() - 1;
  return n > 0 ? s.getRange(2, 1, n, width).getValues() : [];
}

// Last write wins: an incoming row older than the sheet's copy is refused and the sheet's copy sent back.
function upsert(s, width, keyCol, timeCol, items, toRow, rev, device, sortCol) {
  const rows = readAll(s, width), at = {}, add = [], kept = [], applied = [];
  rows.forEach(function (r, i) { at[String(r[keyCol])] = i; });
  items.forEach(function (it) {
    const row = toRow(it, device, rev), key = String(row[keyCol]), i = at[key];
    if (i === undefined) {
      at[key] = rows.length + add.length;
      add.push(row);
      applied.push(row);
      return;
    }
    const cur = i < rows.length ? rows[i] : add[i - rows.length];
    if (stamp(cur[timeCol]) > String(row[timeCol])) {
      kept.push(cur);
      return;
    }
    applied.push(row);
    if (i < rows.length) {
      rows[i] = row;
      s.getRange(i + 2, 1, 1, width).setValues([row]);
    } else {
      add[i - rows.length] = row;
    }
  });
  if (add.length) {
    const first = rows.length + 2, need = first + add.length - 1 - s.getMaxRows();
    if (need > 0) s.insertRowsAfter(s.getMaxRows(), need);
    s.getRange(first, 1, add.length, width).setValues(add);
    const all = rows.concat(add);
    if (sortCol !== null && rows.length && add.some(function (r) { return String(r[sortCol]) < String(rows[rows.length - 1][sortCol]); })) {
      s.getRange(2, 1, all.length, width).sort(sortCol + 1);   // filled-in gaps go back in time order
    }
    return { rows: all, kept: kept, applied: applied };
  }
  return { rows: rows, kept: kept, applied: applied };
}

/* ------------------------------------------------------------------ rows */

function pad(n) { return (n < 10 ? "0" : "") + n; }
function text(v) { v = v === null || v === undefined ? "" : String(v); return /^[=+\-@]/.test(v) ? "'" + v : v; }
function untext(v) { v = v === null || v === undefined ? "" : String(v); return /^'[=+\-@]/.test(v) ? v.slice(1) : v; }
function num(v) { return v === "" || v === null || v === undefined ? null : Number(v); }
function stamp(v) {
  return v instanceof Date
    ? Utilities.formatDate(v, SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone(), "yyyy-MM-dd HH:mm:ss")
    : String(v);
}

function hourRow(h, device, rev) {
  const m = /^h(\d{4})(\d{2})(\d{2})-(\d{2})$/.exec(String(h.key));
  if (!m) throw new Error("bad hour key " + h.key);
  const hr = Number(m[4]);
  return [m[1] + "-" + m[2] + "-" + m[3], pad(hr) + ":00", pad((hr + 1) % 24) + ":00",
          text(h.category), text(h.activity), text(h.remark),
          h.prod === null || h.prod === undefined ? "" : Number(h.prod),
          h.sat === null || h.sat === undefined ? "" : Number(h.sat),
          String(h.status || ""), String(h.logged_at || ""), String(h.key), device, rev];
}

function hourOut(row) {
  return { key: String(row[H.Key]), category: untext(row[H.Category]), activity: untext(row[H.Activity]),
           remark: untext(row[H.Remark]), prod: num(row[H.Productivity]), sat: num(row[H.Satisfaction]),
           status: String(row[H.Status]), logged_at: stamp(row[H["Logged at"]]) };
}

function reminderRow(r, device, rev) {
  return [text(r.text), String(r.time), String(r.days || ""), String(r.date || ""), r.enabled === true,
          String(r.updated_at), r.deleted === true, String(r.id), device, rev,
          r.delete_done === true || r.delete_done === false ? r.delete_done : ""];
}

function reminderOut(row) {
  return { id: String(row[R.Id]), text: untext(row[R.Text]), time: String(row[R.Time]), days: String(row[R.Days]),
           date: String(row[R.Date]) || null, enabled: row[R.On] === true, updated_at: stamp(row[R.Updated]),
           deleted: row[R.Deleted] === true,
           delete_done: row[R["Delete when done"]] === true ? true : row[R["Delete when done"]] === false ? false : null };
}
