/* The entry form shared by the popup and the history editor.
   mountForm() builds it, fillForm() / readForm() move values, formKeydown() is the keyboard. */
const $ = id => document.getElementById(id);
const pad = n => String(n).padStart(2, "0");
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const toDate = s => new Date(s + "T00:00");   // "2026-11-12", local
const iso = d => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
const shift = (s, n) => { const d = toDate(s); d.setDate(d.getDate() + n); return iso(d); };
const nice = s => toDate(s).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

const picked = { cat: null, prod: 3, sat: 3 };
let formCats = [], formRatings = [], digitStep = 0;

function radios(box, values, onPick) {
  box.innerHTML = "";
  for (const v of values) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = v; b.setAttribute("role", "radio");
    b.onclick = () => onPick(v);
    box.append(b);
  }
  box.onkeydown = e => {   // arrow keys move the choice
    const step = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const all = [...box.children], i = all.findIndex(b => b.getAttribute("aria-checked") === "true");
    all[(i + step + all.length) % all.length].click();
  };
}
function mark(box, value, focus) {
  for (const b of box.children) {
    const on = b.textContent == value;
    b.setAttribute("aria-checked", on); b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
  }
}
function pick(key, value, focus) {
  picked[key] = value;
  mark($(key === "cat" ? "cats" : key), value, focus);
}

function mountForm(host, categories, fields) {
  const on = name => !fields || fields[name] !== false;
  host.innerHTML = `
    <section class="chips" id="cats" role="radiogroup" aria-label="Category"></section>
    <label class="field" id="activityField"><span>What did you do?</span><textarea id="activity"></textarea></label>
    <label class="field" id="remarkField"><span>Remark</span><textarea id="remark"></textarea></label>
    <section class="ratings" id="ratings">
      <div class="rating" id="prodBox"><span id="prodLabel">Productivity</span>
        <div class="seg" id="prod" role="radiogroup" aria-labelledby="prodLabel"></div></div>
      <div class="rating" id="satBox"><span id="satLabel">Satisfaction</span>
        <div class="seg" id="sat" role="radiogroup" aria-labelledby="satLabel"></div></div>
    </section>`;
  formCats = categories;
  formRatings = [["prod", "productivity"], ["sat", "satisfaction"]].filter(r => on(r[1])).map(r => r[0]);
  $("remarkField").hidden = !on("remark");
  $("prodBox").hidden = !on("productivity");
  $("satBox").hidden = !on("satisfaction");
  $("ratings").hidden = !formRatings.length;
  radios($("cats"), categories, v => pick("cat", v, true));
  radios($("prod"), [1, 2, 3, 4, 5], v => pick("prod", v, true));
  radios($("sat"), [1, 2, 3, 4, 5], v => pick("sat", v, true));
}

function fillForm(v, focus) {
  $("activity").value = v.activity || ""; $("remark").value = v.remark || "";
  pick("prod", v.prod || 3); pick("sat", v.sat || 3);
  pick("cat", formCats.includes(v.category) ? v.category : formCats[0], focus);
  digitStep = 0;
}

function readForm() {
  return {
    category: picked.cat, activity: $("activity").value.trim(),
    remark: $("remarkField").hidden ? "" : $("remark").value.trim(),
    prod: formRatings.includes("prod") ? picked.prod : null,
    sat: formRatings.includes("sat") ? picked.sat : null };
}

/* Ctrl+Enter saves anywhere. Outside text boxes: Enter saves, digits set the ratings in turn,
   and a letter jumps to the category that starts with it. */
function formKeydown(e, save, enterOk) {
  if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); return save(); }
  const t = e.target;
  if (["TEXTAREA", "INPUT", "SELECT"].includes(t.tagName) || e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.key === "Enter") {
    if (t.closest(".actions") || (enterOk && !enterOk())) return;   // Enter on a button presses that button
    e.preventDefault(); return save();
  }
  if (/^[1-5]$/.test(e.key) && formRatings.length) {
    pick(formRatings[digitStep % formRatings.length], +e.key); digitStep++; return;
  }
  if (/^[a-z]$/i.test(e.key)) {
    const hits = formCats.filter(c => c[0].toLowerCase() === e.key.toLowerCase());
    if (hits.length) pick("cat", hits[(hits.indexOf(picked.cat) + 1) % hits.length], true);
  }
}
