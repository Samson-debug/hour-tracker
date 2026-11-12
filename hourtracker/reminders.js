/* Reminder list and editor, shared by the PC window and the phone page.
   Needs form.js, an element #remHost, and from the page: remCall(name, ...args) -> Promise, onRemSaved(). */
const DAYN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MODE_DAYS = { "Every day": "0123456", "Weekdays": "01234" };
let R = [], editing = null, remMode = "Every day", remBusy = false, remDelTouched = false;
// remDefaultDelete() comes from the page: the "delete one-time reminders once done" setting.
const todayIso = () => iso(new Date());

$("remHost").innerHTML = `
  <div class="remhead"><h1>Reminders</h1><button class="primary" id="remAdd">Add</button></div>
  <p class="hint" id="remHint"></p>
  <div class="rems" id="remList"></div>`;
document.body.insertAdjacentHTML("beforeend", `
<dialog id="remDlg">
  <div class="eyebrow" id="remTitle">New reminder</div>
  <input type="text" id="remText" placeholder="What should I remind you of?" maxlength="120" aria-label="Reminder text">
  <div class="remfield"><span>At</span><input type="time" id="remTime" aria-label="Time"></div>
  <div class="remfield"><span>Repeat</span><div class="seg" id="remMode" role="radiogroup" aria-label="Repeat"></div></div>
  <div class="remfield" id="remDaysRow"><span></span><div class="chips" id="remDays" aria-label="Days"></div></div>
  <div class="remfield" id="remDateRow"><span>On</span><input type="date" id="remDate" aria-label="Date"></div>
  <label class="remfield" id="remDelRow"><span></span><input type="checkbox" id="remDelDone"> Delete it once I mark it done</label>
  <footer class="actions">
    <button class="primary" id="remSave">Save<kbd>↵</kbd></button>
    <button class="ghost" id="remDelete">Delete</button>
    <button class="ghost" id="remCancel">Cancel</button>
    <span class="msg" id="remError" role="alert"></span>
  </footer>
</dialog>`);

function repeatText(r) {
  if (!r.days) return "Once";
  if (r.days === "0123456") return "Every day";
  if (r.days === "01234") return "Weekdays";
  if (r.days === "56") return "Weekends";
  return [...r.days].map(d => DAYN[d]).join(", ");
}
function nextText(r) {
  if (!r.enabled) return "Off";
  if (!r.next) return r.days ? "Done" : nice(r.date) + " · past";
  if (!r.days && (r.delete_done != null ? r.delete_done : remDefaultDelete())) return "Next " + whenText(r.next) + " · deleted once done";
  return "Next " + whenText(r.next);
}
function whenText(next) {
  const [d, t] = next.split(" ");
  return (d === todayIso() ? "today" : d === shift(todayIso(), 1) ? "tomorrow" : nice(d)) + " " + t;
}
async function loadReminders(list) {
  R = list || await remCall("reminders");
  const box = $("remList"); box.innerHTML = "";
  if (!R.length) box.append(el("p", "none", "No reminders yet. Add one for medicine, water, a call…"));
  for (const r of R) {
    const row = el("div", "rem" + (r.enabled ? "" : " off")), main = el("button", "remmain"), what = el("span", "remwhat");
    main.type = "button";
    what.append(el("span", "remtext", r.text), el("span", "remwhen", repeatText(r) + " · " + nextText(r)));
    main.append(el("span", "remtime", r.time), what);
    main.onclick = () => editReminder(r);
    const sw = el("label", "switch"), box2 = el("input");
    box2.type = "checkbox"; box2.checked = r.enabled; box2.dataset.id = r.id;
    box2.setAttribute("aria-label", "Reminder on: " + r.text);
    box2.onchange = async () => { await loadReminders(await remCall("toggle_reminder", r.id, box2.checked)); onRemSaved(); };
    sw.append(box2, el("span"));
    row.append(main, sw); box.append(row);
  }
}

radios($("remMode"), ["Every day", "Weekdays", "Pick days", "Once"], v => setMode(v, true));
DAYN.forEach((n, i) => {
  const b = el("button", null, n); b.type = "button"; b.dataset.d = i;
  b.onclick = () => b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true");
  $("remDays").append(b);
});
function setMode(v, focus) {
  remMode = v; mark($("remMode"), v, focus);
  $("remDaysRow").hidden = v !== "Pick days"; $("remDateRow").hidden = $("remDelRow").hidden = v !== "Once";
}
$("remDelDone").onchange = () => { remDelTouched = true; };
function editReminder(r) {
  editing = r || null;
  const days = r ? r.days : "0123456";
  $("remTitle").textContent = r ? "Edit reminder" : "New reminder";
  $("remText").value = r ? r.text : "";
  $("remTime").value = r ? r.time : pad((new Date().getHours() + 1) % 24) + ":00";
  for (const b of $("remDays").children) b.setAttribute("aria-pressed", days.includes(b.dataset.d));
  $("remDate").min = todayIso(); $("remDate").value = r && r.date ? r.date : todayIso();
  setMode(!r ? "Every day" : !days ? "Once" : days === "0123456" ? "Every day" : days === "01234" ? "Weekdays" : "Pick days");
  $("remDelete").hidden = !r; $("remDelete").textContent = "Delete"; delete $("remDelete").dataset.sure;
  remDelTouched = false;   // untouched, it keeps following the default setting
  $("remDelDone").checked = r && r.delete_done != null ? r.delete_done : remDefaultDelete();
  $("remError").textContent = "";
  $("remDlg").showModal(); $("remText").focus();
}
async function remSave() {
  if (remBusy) return;
  const text = $("remText").value.trim(), time = $("remTime").value;
  let days = MODE_DAYS[remMode] || "", date = null;
  if (remMode === "Pick days")
    days = [...$("remDays").children].filter(b => b.getAttribute("aria-pressed") === "true").map(b => b.dataset.d).join("");
  if (remMode === "Once") date = $("remDate").value;
  const err = !text ? "Write what to remind you of." : !time ? "Pick a time." :
    remMode === "Pick days" && !days ? "Pick at least one day." :
    remMode === "Once" && (!date || new Date(date + "T" + time) <= new Date()) ? "Pick a time that is still ahead." : "";
  if (err) { $("remError").textContent = err; return; }
  remBusy = true;
  try {
    const delete_done = remMode !== "Once" ? null : remDelTouched ? $("remDelDone").checked : editing ? editing.delete_done : null;
    const list = await remCall("save_reminder", { id: editing ? editing.id : null, enabled: editing ? editing.enabled : true, text, time, days, date, delete_done });
    $("remDlg").close(); await loadReminders(list); onRemSaved();
  } catch (err) {
    $("remError").textContent = "Could not save. Try again.";
  } finally { remBusy = false; }
}
$("remAdd").onclick = () => editReminder(null);
$("remSave").onclick = remSave;
$("remCancel").onclick = () => $("remDlg").close();
$("remDelete").onclick = async () => {
  const b = $("remDelete");
  if (!b.dataset.sure) { b.dataset.sure = 1; b.textContent = "Delete for good?"; return; }
  const list = await remCall("delete_reminder", editing.id);
  $("remDlg").close(); await loadReminders(list); onRemSaved();
};
document.addEventListener("keydown", e => {
  if ($("remDlg").open && e.key === "Enter" && (e.ctrlKey || e.target.id === "remText")) { e.preventDefault(); remSave(); }
});
